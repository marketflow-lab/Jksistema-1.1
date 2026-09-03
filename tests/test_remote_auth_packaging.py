from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_carries_public_remote_auth_bootstrap_and_client_module():
    config = json.loads((ROOT / "electron_app" / "client-config.json").read_text(encoding="utf-8"))
    remote = config["remoteAuth"]
    assert remote == {
        "url": "https://jkjkjk-485920.web.app/api/auth/v1/login",
        "projectId": "jkjkjk-485920",
        "mode": "prefer",
    }

    manifest = json.loads(
        (ROOT / "electron_app" / "installer-required-resources.json").read_text(encoding="utf-8")
    )
    assert "electron_app/client-config.json" in manifest["requiredSourceFiles"]
    assert "backend/services/remote_auth_client.py" in manifest["requiredSourceFiles"]
    assert "backend/services/remote_auth_contracts.py" in manifest["requiredSourceFiles"]
    assert "backend/services/remote_auth_verification.py" in manifest["requiredSourceFiles"]
    assert "client-config.json" in manifest["requiredPackagedFiles"]
    assert "local_app/backend/services/remote_auth_client.py" in manifest["requiredPackagedFiles"]
    assert "local_app/backend/services/remote_auth_contracts.py" in manifest["requiredPackagedFiles"]
    assert "local_app/backend/services/remote_auth_verification.py" in manifest["requiredPackagedFiles"]
    assert "backend/services/remote_auth_client.py" in manifest["requiredPackagedSourceParity"]
    assert "backend/services/remote_auth_contracts.py" in manifest["requiredPackagedSourceParity"]
    assert "backend/services/remote_auth_verification.py" in manifest["requiredPackagedSourceParity"]


def test_public_bootstrap_contains_no_private_key_or_desktop_secret():
    config_text = (ROOT / "electron_app" / "client-config.json").read_text(encoding="utf-8").lower()
    forbidden = (
        "private_key",
        "client_secret",
        "service_account",
        "refresh_token",
        "password",
        "firebase_web_api_key",
    )
    assert not any(marker in config_text for marker in forbidden)


def test_electron_bootstrap_pins_bundled_auth_endpoint_and_injects_both_start_paths():
    source = (ROOT / "electron_app" / "main" / "modules" / "backend.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function getLocalBackendRemoteAuthEnv()")
    end = source.index("let backendRuntimeMaterializerModule", start)
    bootstrap = source[start:end]

    assert "paths.bundledConfig" in bootstrap
    assert "paths.devConfig" in bootstrap
    assert "loadClientConfig()" not in bootstrap
    assert "process.env.JK_REMOTE_AUTH_URL || remoteAuth.url" in bootstrap
    assert '`set "JK_REMOTE_AUTH_URL=${cmdValue(remoteAuthEnv.JK_REMOTE_AUTH_URL)}"`' in source
    assert "...remoteAuthEnv" in source
