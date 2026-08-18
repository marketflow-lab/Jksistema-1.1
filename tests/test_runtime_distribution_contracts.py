import hashlib
import json
from pathlib import Path

from backend.services import codex_console, whatsapp_bridge
from backend.services.codex.console import runtime as console_runtime


def test_release_version_is_canonical_and_materialized():
    root = Path(__file__).resolve().parents[1]
    root_package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    electron_package = json.loads((root / "electron_app" / "package.json").read_text(encoding="utf-8"))
    context_manifest = json.loads((root / "context-bundle-manifest.json").read_text(encoding="utf-8"))
    runtime_manifest = json.loads((root / ".installer_runtime" / "runtime-manifest.json").read_text(encoding="utf-8-sig"))

    release_version = electron_package["version"]
    assert release_version == "1.0.123"
    assert root_package["version"] == release_version
    assert context_manifest["source_version"] == release_version
    assert runtime_manifest["version"] == release_version

    local_app_resource = next(
        item for item in electron_package["build"]["extraResources"] if item.get("to") == "local_app"
    )
    assert "package.json" in local_app_resource["filter"]


def test_codex_status_distinguishes_authentication_pending(monkeypatch):
    monkeypatch.setattr(console_runtime, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_enabled", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_auth_detected", lambda: False)
    monkeypatch.setattr(console_runtime, "_codex_cli_version", lambda: (True, "codex.exe"))
    monkeypatch.setattr(console_runtime, "_codex_runtime_diagnostics",
        lambda: {
            "path": "codex.exe",
            "version": "test",
            "config_ok": True,
            "config_error": "",
        },
    )

    payload = console_runtime._codex_status_payload()

    assert payload["ready"] is True
    assert payload["runtime_status"] == "authentication_pending"
    assert payload["authentication_required"] is True
    assert payload["sdk_installed"] is True
    assert payload["cli_available"] is True


def test_gateway_health_rejects_incompatible_protocol(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_gateway_json",
        lambda *_args, **_kwargs: {
            "success": True,
            "worker": True,
            "gateway_protocol_version": 999,
        },
    )

    payload = whatsapp_bridge._worker_health({"worker_url": "https://example.test", "bridge_token": "token"})

    assert payload["success"] is False
    assert payload["protocol_compatible"] is False
    assert "gateway_protocol_incompatible" in payload["error"]


def test_whisper_prefers_verified_bundled_model(monkeypatch, tmp_path):
    base = tmp_path / "app"
    info = tmp_path / "info"
    model = base / "black_jhon_runtime" / "faster-whisper-small"
    artifact = model / "snapshot" / "model.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"offline-whisper")
    manifest = {
        "model": "small",
        "engine": "faster-whisper==1.2.1",
        "files": [
            {
                "path": "snapshot/model.bin",
                "size": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
    }
    (model / "model-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(whatsapp_bridge, "_base_dir", lambda: base)
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: info)
    whatsapp_bridge.MODEL_VALIDATION_CACHE.clear()

    selected = whatsapp_bridge._model_dir()

    assert selected == model.resolve()
    assert whatsapp_bridge._validate_model_dir(selected)["valid"] is True


def test_whisper_runner_resolves_service_script():
    expected = Path(whatsapp_bridge.__file__).with_name("whatsapp_transcribe.py").resolve()

    runner = whatsapp_bridge._whisper_runner()

    assert runner == expected
    assert runner.is_file()
